"""Radarr CLI (``arr_cli.radarr``) — task 9.

This module is the Radarr entry point for the ``arr-cli`` MVP. It
exposes six read-only commands against a live Radarr instance:

* ``calendar [start [end]]`` -- ``GET /api/v3/calendar`` (REQ-7 AC1, AC2)
* ``wanted``                 -- ``GET /api/v3/wanted/missing`` (REQ-7 AC3)
* ``queue``                  -- ``GET /api/v3/queue`` (REQ-7 AC4)
* ``recent``                 -- ``GET /api/v3/history/movie`` (REQ-7 AC5)
* ``lookup <term>``          -- ``GET /api/v3/movie/lookup?term=<urlencoded term>`` (REQ-7 AC6)
* ``movie <id>``             -- ``GET /api/v3/movie/{id}`` (REQ-7 AC7)

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
from arr_cli.facade.cli_common import build_parser, main_wrapper
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
    columns = [
        "title",
        "year",
        "movieFile",
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

    Note: Radarr's movie history is at ``/api/v3/history/movie`` (NOT
    ``/api/v3/history`` like Sonarr). The path is hardcoded here per
    the spec to keep both CLIs independent.
    """
    payload = _get(
        "/api/v3/history/movie",
        args,
        cfg,
        op="recent",
    )
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
        "monitored",
    ]
    return _emit(payload, args, columns=columns)


def cmd_movie(args: argparse.Namespace, cfg: ServiceConfig) -> int:
    """Radarr ``movie <id>`` -- fetch a single movie by id (REQ-7 AC7).

    The transport layer maps a 404 response to
    :class:`HttpError(exit_code=4)` so the caller doesn't need to
    inspect the status code; :func:`main_wrapper` then emits the
    structured stderr line naming the id.
    """
    raw_id = getattr(args, "movie_id", "")
    movie_id = transport.encode_path_segment(raw_id)
    payload = _get(
        f"/api/v3/movie/{movie_id}",
        args,
        cfg,
        op=f"movie id={raw_id}",
    )
    columns = [
        "title",
        "year",
        "runtime",
        "genres",
        "monitored",
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
            "recent history, lookup-by-term, and single movie by id."
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
    )

    subparsers.add_parser(
        "queue",
        help="list the current download/import queue (GET /api/v3/queue)",
    )

    subparsers.add_parser(
        "recent",
        help="list recent movie history (GET /api/v3/history/movie)",
    )

    lookup = subparsers.add_parser(
        "lookup",
        help="lookup a movie by term (GET /api/v3/movie/lookup?term=...)",
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
        help="fetch a single movie by id (GET /api/v3/movie/{id})",
    )
    movie.add_argument(
        "movie_id",
        metavar="ID",
        help="Radarr movie id (percent-encoded before being sent)",
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