"""Radarr CLI (``arr_cli.radarr``) — task 9.

This module is the Radarr entry point for the ``arr-cli`` MVP. It
exposes six read-only commands against a live Radarr instance:

* ``calendar [start [end]]`` -- ``GET /api/v3/calendar`` (REQ-7 AC1, AC2)
* ``wanted``                 -- ``GET /api/v3/wanted/missing`` (REQ-7 AC3)
* ``queue``                  -- ``GET /api/v3/queue`` (REQ-7 AC4)
* ``recent``                 -- ``GET /api/v3/history?includeMovie=true&pageSize=<N>`` (REQ-7 AC5)
* ``lookup <term>``          -- ``GET /api/v3/movie/lookup?term=<urlencoded term>`` (REQ-7 AC6)
* ``movie [<id>]``           -- ``GET /api/v3/movie`` (REQ-2 AC1) or
                                ``GET /api/v3/movie/{id}`` (REQ-7 AC7, REQ-3 AC2)

Per the MVP design, every command is a thin wrapper that:

1. Builds the Radarr path (with percent-encoded path segments)
2. Calls :func:`arr_cli.facade.transport.get` to fetch the JSON
3. Renders the payload via :func:`arr_cli.facade.output.emit` (JSON
   pass-through by default, tabular ``--human`` rendering when
   requested)
4. Returns ``0`` on success or reraises the documented
   :class:`ArrError` subclass so :func:`main_wrapper` can translate
   the error into the structured ``service=...`` stderr line and the
   documented exit code.

Following the design contract this module contains no hardcoded URL
or credential; it reads configuration via :class:`ServiceConfig` and
delegates transport / auth / output to the facade. The console-script
entry point declared in ``pyproject.toml`` (``radarr = \
"arr_cli.radarr:main"``) calls :func:`main` which builds the
argparse subparser tree and delegates to the universal
:func:`arr_cli.facade.cli_common.main_wrapper` for config loading and
error mapping.
"""

from __future__ import annotations

import argparse
import re
from typing import Any, Sequence

from arr_cli.facade import output, transport
from arr_cli.facade.cli_common import build_parser, main_wrapper, universal_parents
from arr_cli.facade.config import ServiceConfig
from arr_cli.facade.errors import ConfigError

__all__ = [
    "main",
    "build_radarr_parser",
    # Command handlers are exposed for tests; they are not part of the
    # public CLI surface but unit tests use them to verify routing in
    # isolation from the argparse layer.
    "cmd_calendar",
    "cmd_wanted",
    "cmd_queue",
    "cmd_recent",
    "cmd_lookup",
    "cmd_movie",
    # ``_validate_iso_date`` is re-exported so the Sonarr module
    # (task 10) can import it from here per the design contract
    # ("Re-export ``_validate_iso_date`` or copy the 6-line validator").
    "_validate_iso_date",
    # ``_validate_page_size`` is the ``--page-size`` validator for
    # ``radarr recent``; exported so tests can exercise the bounds
    # directly without going through the argparse layer.
    "_validate_page_size",
]


#: Service identifier used by transport.get / output.emit.
#: Centralised so a future rename touches one constant.
SERVICE_NAME = "radarr"


#: Regex matching the two ISO-8601 forms Radarr's calendar endpoint
#: accepts per REQ-7 AC2: a calendar date (``YYYY-MM-DD``) or an
#: ISO-8601 datetime with seconds and an optional ``Z`` suffix
#: (``YYYY-MM-DDTHH:MM:SSZ?``). Other formats -- e.g. ``next-tuesday``
#: or ``2026/01/01`` -- raise :class:`ConfigError` so the operator
#: sees a usage hint on stderr and the documented exit code 1.
_ISO_DATE_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}:\d{2}Z?)?$"
)


#: Bounds for ``--page-size`` on ``radarr recent`` (the activity-log
#: cap). The renderer projects the full row anyway so the cap
#: protects operators from accidentally pulling thousands of rows on
#: a heavily-used library; the upper bound matches the upstream
#: page-size ceiling exposed by Radarr's ``/api/v3/history``.
_PAGE_SIZE_MIN = 1
_PAGE_SIZE_MAX = 1000
_PAGE_SIZE_DEFAULT = 10


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_iso_date(value: str) -> str:
    """Return ``value`` if it parses as ``YYYY-MM-DD`` or ``YYYY-MM-DDTHH:MM:SS[Z]``.

    Per REQ-7 AC2 (and REQ-8 AC2 for Sonarr, which reuses this helper)
    the calendar endpoint accepts both forms; anything else MUST
    raise :class:`ConfigError` with exit code 1 so the operator
    receives the documented stderr usage hint and a non-zero exit.

    The validator is intentionally strict: the requirements name
    "ISO-8601 dates and ISO-8601 datetimes" and reject other formats.
    A loose ``dateutil`` parse would accept ambiguous inputs (e.g.
    ``2026-1-1``) which the spec disallows.
    """
    if not isinstance(value, str) or not _ISO_DATE_RE.match(value):
        raise ConfigError(
            SERVICE_NAME,
            "calendar",
            (
                f"{SERVICE_NAME}: calendar — invalid date {value!r}; "
                "expected ISO-8601 date (YYYY-MM-DD) or datetime "
                "(YYYY-MM-DDTHH:MM:SS[Z])"
            ),
        )
    return value


def _validate_page_size(value: Any) -> int:
    """Return ``value`` as an int if it parses in ``[1, 1000]``.

    Used as the ``type=`` callback for ``--page-size`` on
    ``radarr recent`` (the activity-log cap). Out-of-range values
    raise :class:`argparse.ArgumentTypeError` so argparse prints a
    usage hint naming the offending value and calls ``sys.exit(2)``;
    :func:`arr_cli.facade.cli_common.main_wrapper` translates that
    SystemExit into the documented exit code ``1`` (via the
    ``config=parse`` ConfigError path). The validator runs before
    the HTTP call so a bad value never reaches ``transport.get``.

    The bounds mirror Radarr's ``/api/v3/history`` upstream
    page-size ceiling: the upper bound prevents an operator from
    accidentally pulling a million-row history page, and the lower
    bound of ``1`` rejects ``0`` and negatives (both would yield
    an empty ``records`` list with no diagnostic).
    """
    try:
        n = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError(
            f"--page-size: invalid value {value!r}; "
            f"expected integer in [{_PAGE_SIZE_MIN}, {_PAGE_SIZE_MAX}]"
        )
    if n < _PAGE_SIZE_MIN or n > _PAGE_SIZE_MAX:
        raise argparse.ArgumentTypeError(
            f"--page-size: invalid value {n}; "
            f"must be between {_PAGE_SIZE_MIN} and {_PAGE_SIZE_MAX} "
            "(inclusive)"
        )
    return n


def _emit(
    payload: Any,
    args: argparse.Namespace,
    *,
    columns: Sequence[str] | None = None,
) -> int:
    """Render ``payload`` via :func:`output.emit` and return exit code 0.

    Centralised so every command handler has the same JSON-vs-tabular
    decision point; the per-command handler only decides which columns
    to surface when ``--human`` is requested.
    """
    output.emit(
        payload,
        human_mode=bool(getattr(args, "human", False)),
        verbose_mode=bool(getattr(args, "verbose", False)),
        service=SERVICE_NAME,
        command=str(getattr(args, "command", "") or ""),
        columns=columns,
        limit=int(getattr(args, "limit", 20) or 20),
    )
    return 0


def _get(
    path: str,
    args: argparse.Namespace,
    cfg: ServiceConfig,
    *,
    params: dict[str, Any] | None = None,
    op: str | None = None,
) -> Any:
    """Thin wrapper around :func:`transport.get` that honors per-call flags.

    The ``connect_timeout`` / ``read_timeout`` flags are forwarded from
    the parsed argparse namespace so :func:`main_wrapper`'s
    "CLI overrides config" policy applies uniformly (REQ-5 AC3).
    """
    return transport.get(
        SERVICE_NAME,
        path,
        params=params,
        cfg=cfg,
        connect_timeout=getattr(args, "connect_timeout", None),
        read_timeout=getattr(args, "read_timeout", None),
        debug=bool(getattr(args, "debug", False)),
    )


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


def cmd_calendar(args: argparse.Namespace, cfg: ServiceConfig) -> int:
    """Radarr ``calendar [start [end]]`` -- upcoming releases (REQ-7 AC1, AC2).

    The endpoint accepts optional ``start`` and ``end`` query
    parameters; both MUST be either a ``YYYY-MM-DD`` date or an
    ISO-8601 ``YYYY-MM-DDTHH:MM:SS[Z]`` datetime. Invalid inputs
    raise :class:`ConfigError(exit_code=1)` with a stderr usage hint
    that names the offending value.
    """
    params: dict[str, Any] = {}
    start = getattr(args, "start", None)
    end = getattr(args, "end", None)
    if start is not None:
        params["start"] = _validate_iso_date(start)
    if end is not None:
        params["end"] = _validate_iso_date(end)

    payload = _get(
        "/api/v3/calendar",
        args,
        cfg,
        params=params or None,
        op="calendar",
    )
    columns = [
        "title",
        "year",
        "inCinemas",
        "physicalRelease",
        "digitalRelease",
    ]
    return _emit(payload, args, columns=columns)


def cmd_wanted(args: argparse.Namespace, cfg: ServiceConfig) -> int:
    """Radarr ``wanted`` -- movies that are missing and monitored (REQ-7 AC3)."""
    payload = _get(
        "/api/v3/wanted/missing",
        args,
        cfg,
        op="wanted",
    )
    # Tabular columns match the summary-shape keys emitted by
    # ``_summary_radarr_wanted`` (flat top-level keys); the summary
    # renderer does not emit ``movieFile`` so it is intentionally
    # absent from the column list.
    columns = [
        "title",
        "year",
        "tmdbId",
        "monitored",
    ]
    return _emit(payload, args, columns=columns)


def cmd_queue(args: argparse.Namespace, cfg: ServiceConfig) -> int:
    """Radarr ``queue`` -- current download/import queue (REQ-7 AC4)."""
    payload = _get(
        "/api/v3/queue",
        args,
        cfg,
        op="queue",
    )
    # Tabular columns match the summary-shape keys emitted by
    # ``_summary_radarr_queue`` (flat top-level keys).
    columns = [
        "title",
        "status",
        "trackedDownloadStatus",
        "size",
        "sizeleft",
    ]
    return _emit(payload, args, columns=columns)


def cmd_recent(args: argparse.Namespace, cfg: ServiceConfig) -> int:
    """Radarr ``recent`` -- recent movie history (REQ-7 AC5).

    Hits ``GET /api/v3/history?includeMovie=true&pageSize=<N>``
    (the activity-log endpoint), NOT ``/api/v3/history/movie``.
    The per-movie endpoint only returns rows for a single
    ``movieId`` and never populates a nested ``movie`` envelope,
    so it cannot satisfy "recent events across the library". The
    activity-log endpoint returns a paginated
    ``{page, pageSize, sortKey, sortDirection, totalRecords,
    records: [...]}`` envelope; we unwrap it to the bare
    ``records`` list before rendering so the summary renderer
    and the ``--human`` table iterate the rows directly. The
    renderer contract (nested ``movie: {title, year}``) is
    preserved unchanged.

    The ``--page-size`` argparse flag (default ``10``) overrides
    the page-size query parameter at the upstream boundary; the
    ``_validate_page_size`` helper bounds it to
    ``[1, 1000]`` so an operator cannot accidentally request a
    million-row history page.
    """
    page_size = getattr(args, "page_size", _PAGE_SIZE_DEFAULT)
    payload = _get(
        "/api/v3/history",
        args,
        cfg,
        params={
            "includeMovie": "true",
            "pageSize": int(page_size),
        },
        op="recent",
    )
    # ``/api/v3/history`` returns the paginated activity-log envelope
    # on Radarr v3; unwrap to the bare ``records`` list so the
    # renderer and ``--human`` paths iterate the rows directly. A
    # bare-list payload (defensive fallback) is unchanged.
    payload = output._unwrap_envelope(payload)
    # Tabular columns match the summary-shape keys emitted by
    # ``_summary_radarr_recent``: nested ``movie.title`` /
    # ``movie.year`` are resolved via dot-path traversal in
    # ``_row_from_mapping``.
    columns = [
        "movie.title",
        "movie.year",
        "eventType",
        "date",
    ]
    return _emit(payload, args, columns=columns)


def cmd_lookup(args: argparse.Namespace, cfg: ServiceConfig) -> int:
    """Radarr ``lookup <term>`` -- lookup a movie by title (REQ-7 AC6).

    The ``term`` parameter is forwarded as a query string; the
    transport layer percent-encodes the value so special characters
    (slashes, spaces, ``?``, ``&``) cannot break the URL.

    the ``monitored`` field on these records is the source default
    (TMDB for Radarr, TVDB for Sonarr), not the user's library
    state. The ``id`` column disambiguates library rows (numeric
    ``id``, real ``added`` and ``path``) from candidates (no ``id``,
    placeholder ``added='0001-01-01T00:01:00Z'``, no ``path``); for
    a clean library listing use ``radarr movie``.
    """
    term = getattr(args, "term", "") or ""
    payload = _get(
        "/api/v3/movie/lookup",
        args,
        cfg,
        params={"term": term},
        op="lookup",
    )
    columns = [
        "title",
        "year",
        "tmdbId",
        "imdbId",
        "id",
        "monitored",
    ]
    return _emit(payload, args, columns=columns)


def cmd_movie(args: argparse.Namespace, cfg: ServiceConfig) -> int:
    """Radarr ``movie [<id>]`` -- list every movie, or fetch one by id.

    When ``movie_id`` is supplied the CLI hits the single-fetch
    endpoint ``GET /api/v3/movie/{id}`` (REQ-7 AC7, REQ-3 AC2);
    when omitted it lists every movie in the library via
    ``GET /api/v3/movie`` (REQ-2 AC1). The transport layer maps a
    404 on the single-fetch path to :class:`HttpError(exit_code=4)`
    so the caller doesn't need to inspect the status code;
    :func:`main_wrapper` then emits the structured stderr line
    naming the id.
    """
    raw_id = getattr(args, "movie_id", None)
    if raw_id:
        encoded_id = transport.encode_path_segment(raw_id)
        payload = _get(
            f"/api/v3/movie/{encoded_id}",
            args,
            cfg,
            op=f"movie id={raw_id}",
        )
        # Single-id row is the operator's library row; columns match
        # the original REQ-7 AC7 contract and stay unchanged here
        # (REQ-4 only renames the column on the lookup endpoint).
        columns = [
            "title",
            "year",
            "runtime",
            "genres",
            "monitored",
        ]
    else:
        payload = _get(
            "/api/v3/movie",
            args,
            cfg,
            op="movie",
        )
        # Library-list columns are pinned by REQ-2 AC2: the rendered
        # table MUST show exactly these six columns in this order.
        # ``monitored`` here is the operator's library flag on a
        # library row, not the source default that REQ-4 renames.
        columns = [
            "title",
            "year",
            "monitored",
            "status",
            "tmdbId",
            "imdbId",
        ]
    return _emit(payload, args, columns=columns)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


#: Mapping of subcommand name → handler. Used by the post-parser
#: dispatch step in :func:`main` so every command has a single,
#: auditable registration point. The names are the exact strings the
#: ``argparse`` subparser registers.
_DISPATCH = {
    "calendar": cmd_calendar,
    "wanted": cmd_wanted,
    "queue": cmd_queue,
    "recent": cmd_recent,
    "lookup": cmd_lookup,
    "movie": cmd_movie,
}


def _dispatch(args: argparse.Namespace, cfg: ServiceConfig) -> int:
    """Invoke the handler selected by ``args.command`` and return its exit code."""
    handler = _DISPATCH.get(args.command)
    if handler is None:
        # Defensive: argparse normally rejects unknown subcommands at
        # parse time; this branch covers a future bug where the
        # dispatch table falls out of sync with the subparser list.
        raise ConfigError(
            SERVICE_NAME,
            "dispatch",
            f"{SERVICE_NAME}: unknown command {args.command!r}",
        )
    return handler(args, cfg)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def build_radarr_parser() -> argparse.ArgumentParser:
    """Build the full argparse parser for the ``radarr`` CLI.

    The returned parser already includes the universal flag set
    (registered by :func:`arr_cli.facade.cli_common.build_parser`) and
    the six Radarr subcommands. Exposed for tests so they can parse
    arguments without going through the console-script entry point.
    """
    parser = build_parser(
        prog=SERVICE_NAME,
        description=(
            "Read-only CLI for Radarr. Six commands expose upcoming "
            "calendar, missing/wanted movies, the download queue, "
            "recent history, lookup-by-term, and movie by id (or the "
            "full movie library when no id is given)."
        ),
    )
    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
        metavar="COMMAND",
        help="Radarr subcommand (see below)",
    )

    calendar = subparsers.add_parser(
        "calendar",
        help=(
            "list upcoming releases "
            "(GET /api/v3/calendar[start=<start>][end=<end>])"
        ),
        parents=universal_parents(),
        add_help=False,
    )
    calendar.add_argument(
        "start",
        nargs=argparse.OPTIONAL,
        default=None,
        metavar="START",
        help=(
            "optional ISO-8601 date (YYYY-MM-DD) or datetime "
            "(YYYY-MM-DDTHH:MM:SS[Z]) for the calendar lower bound"
        ),
    )
    calendar.add_argument(
        "end",
        nargs=argparse.OPTIONAL,
        default=None,
        metavar="END",
        help=(
            "optional ISO-8601 date (YYYY-MM-DD) or datetime "
            "(YYYY-MM-DDTHH:MM:SS[Z]) for the calendar upper bound"
        ),
    )

    subparsers.add_parser(
        "wanted",
        help="list missing monitored movies (GET /api/v3/wanted/missing)",
        parents=universal_parents(),
        add_help=False,
    )

    subparsers.add_parser(
        "queue",
        help="list the current download/import queue (GET /api/v3/queue)",
        parents=universal_parents(),
        add_help=False,
    )

    recent = subparsers.add_parser(
        "recent",
        help=(
            "list recent movie history "
            "(GET /api/v3/history?includeMovie=true&pageSize=<N>)"
        ),
        parents=universal_parents(),
        add_help=False,
    )
    recent.add_argument(
        "--page-size",
        type=_validate_page_size,
        default=_PAGE_SIZE_DEFAULT,
        metavar="N",
        help=(
            "number of history rows to fetch from the activity-log "
            f"endpoint (1-{_PAGE_SIZE_MAX}; default {_PAGE_SIZE_DEFAULT})"
        ),
    )

    lookup = subparsers.add_parser(
        "lookup",
        help="lookup a movie by term (GET /api/v3/movie/lookup?term=...)",
        parents=universal_parents(),
        add_help=False,
    )
    lookup.add_argument(
        "term",
        nargs=argparse.OPTIONAL,
        default="",
        metavar="TERM",
        help="search term (percent-encoded before being sent)",
    )

    movie = subparsers.add_parser(
        "movie",
        help=(
            "fetch a single movie by id, or list all movies when no "
            "id is given (GET /api/v3/movie[/{id}])"
        ),
        parents=universal_parents(),
        add_help=False,
    )
    movie.add_argument(
        "movie_id",
        nargs=argparse.OPTIONAL,
        default=None,
        metavar="ID",
        help=(
            "optional Radarr movie id (percent-encoded before being "
            "sent); omit to list every movie in the library"
        ),
    )

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """Console-script entry point for the ``radarr`` executable.

    Wires the per-service subparser tree to :func:`main_wrapper` so
    config loading, debug/quiet flag handling, and ArrError → stderr
    + exit-code mapping all happen in one place.
    """
    parser = build_radarr_parser()
    return main_wrapper(
        SERVICE_NAME,
        _dispatch,
        parser=parser,
        argv=argv,
    )