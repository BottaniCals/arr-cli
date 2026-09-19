"""Jellyfin CLI (``arr_cli.jellyfin``) — task 8.

This module is the Jellyfin entry point for the ``arr-cli`` MVP. It
exposes eight read-only commands against a live Jellyfin instance:

* ``now``         — ``GET /Sessions`` (REQ-6 AC1)
* ``resume``      — ``GET /Users/{user_id}/Items/Resume`` (REQ-6 AC2)
* ``recent``      — ``GET /Users/{user_id}/Items?SortBy=DatePlayed&Filters=IsPlayed`` (REQ-6 AC3)
* ``nextup``      — ``GET /Shows/NextUp`` (REQ-6 AC4)
* ``latest``      — ``GET /Users/{user_id}/Items/Latest`` (REQ-6 AC5)
* ``search``      — ``GET /Items?searchTerm=<urlencoded query>`` (REQ-6 AC6)
* ``item``        — ``GET /Items/{id}`` (REQ-6 AC7)
* ``favorites``   — ``GET /Users/{user_id}/Items?Filters=IsFavorite`` (REQ-6 AC8)

Per the MVP design, every command is a thin wrapper that:

1. Builds the Jellyfin path (with percent-encoded path segments)
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
entry point declared in ``pyproject.toml`` (``jellyfin = \
"arr_cli.jellyfin:main"``) calls :func:`main` which builds the
argparse subparser tree and delegates to the universal
:func:`arr_cli.facade.cli_common.main_wrapper` for config loading and
error mapping.
"""

from __future__ import annotations

import argparse
from typing import Any, Sequence

from arr_cli.facade import output, transport
from arr_cli.facade.cli_common import build_parser, main_wrapper, universal_parents
from arr_cli.facade.config import ServiceConfig
from arr_cli.facade.errors import ConfigError

__all__ = [
    "main",
    "build_jellyfin_parser",
    # Command handlers are exposed for tests; they are not part of the
    # public CLI surface but unit tests use them to verify routing in
    # isolation from the argparse layer.
    "cmd_now",
    "cmd_resume",
    "cmd_recent",
    "cmd_nextup",
    "cmd_latest",
    "cmd_search",
    "cmd_item",
    "cmd_favorites",
]


#: Service identifier used by transport.get / output.emit / warn_once.
#: Centralised so a future rename touches one constant.
SERVICE_NAME = "jellyfin"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_user_id(cfg: ServiceConfig) -> str:
    """Return ``cfg.jellyfin.user_id`` or raise :class:`ConfigError`.

    The Jellyfin endpoints that target a specific user (``resume``,
    ``recent``, ``latest``, ``favorites``) all require a configured
    ``jellyfin.user_id``. If the field is missing from the parsed
    config we raise :class:`ConfigError` rather than dispatching to
    the service with an empty path segment (REQ-1 AC6, REQ-6 AC2-5,
    REQ-6 AC8).
    """
    auth = cfg.jellyfin
    if auth is None:
        raise ConfigError(
            SERVICE_NAME,
            "load",
            (
                f"{SERVICE_NAME}: section missing in arr.conf — add a "
                f"{SERVICE_NAME}: block with url, {transport.AK_LITERAL}, "
                "and user_id"
            ),
        )
    if not auth.user_id:
        raise ConfigError(
            SERVICE_NAME,
            "load",
            (
                f"{SERVICE_NAME}: user_id missing — set "
                f"{SERVICE_NAME}.user_id in arr.conf"
            ),
        )
    return auth.user_id


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


def cmd_now(args: argparse.Namespace, cfg: ServiceConfig) -> int:
    """Jellyfin ``now`` — emit the current active sessions.

    ``GET /Sessions`` -- REQ-6 AC1. No path-level user id; the
    endpoint returns all sessions across all users.
    """
    payload = _get("/Sessions", args, cfg, op="now")
    # Tabular columns match the summary-shape keys emitted by
    # ``_summary_jellyfin_now`` (flat top-level keys plus nested
    # ``playing.*`` and ``progress.*`` dot-paths; see
    # ``arr_cli.facade.output._SUMMARY_RENDERERS``). The empty list
    # when no sessions are active is rendered as ``(empty list)`` by
    # the human renderer.
    columns = [
        "user",
        "device",
        "client",
        "playing.type",
        "playing.name",
        "playing.series",
        "playing.season",
        "playing.episode",
        "progress.position_ticks",
        "progress.is_paused",
    ]
    return _emit(payload, args, columns=columns)


def cmd_resume(args: argparse.Namespace, cfg: ServiceConfig) -> int:
    """Jellyfin ``resume`` — items the user can resume (REQ-6 AC2)."""
    user_id = _require_user_id(cfg)
    payload = _get(
        f"/Users/{transport.encode_path_segment(user_id)}/Items/Resume",
        args,
        cfg,
        op="resume",
    )
    # Tabular columns match the summary-shape keys emitted by
    # ``_summary_jellyfin_resume``: the renderer flattens
    # ``UserData.PlaybackPositionTicks`` and ``UserData.PlayCount``
    # as top-level keys with ``.`` so the human renderer resolves
    # them via dot-path traversal in ``_row_from_mapping``.
    columns = [
        "Name",
        "Type",
        "ProductionYear",
        "SeriesName",
        "UserData.PlaybackPositionTicks",
        "UserData.PlayCount",
    ]
    return _emit(payload, args, columns=columns)


def cmd_recent(args: argparse.Namespace, cfg: ServiceConfig) -> int:
    """Jellyfin ``recent`` — items the user has already played (REQ-6 AC3).

    The endpoint accepts ``SortBy=DatePlayed``, ``Filters=IsPlayed``,
    and ``includeItemTypes`` as query parameters. The transport layer
    percent-encodes the values automatically.

    ``includeItemTypes`` is required for v12 servers: GetItems is now
    asynchronous and applies recursive expansion only when filters are
    requested together with ``includeItemTypes`` (see the v12 release
    notes, "API Changes"). Without it the v12 server returns a single
    episode rather than the rolled-up set 10.11 produced. ``Movie`` and
    ``Episode`` mirror the operator's recent-played expectation.
    """
    user_id = _require_user_id(cfg)
    payload = _get(
        f"/Users/{transport.encode_path_segment(user_id)}/Items",
        args,
        cfg,
        params={
            "SortBy": "DatePlayed",
            "Filters": "IsPlayed",
            "includeItemTypes": "Movie,Episode",
        },
        op="recent",
    )
    # Tabular columns match the summary-shape keys emitted by
    # ``_summary_jellyfin_recent``: the renderer flattens
    # ``UserData.LastPlayedDate`` as a top-level key with ``.`` so
    # the human renderer resolves it via dot-path traversal in
    # ``_row_from_mapping``.
    columns = [
        "Name",
        "Type",
        "ProductionYear",
        "SeriesName",
        "UserData.LastPlayedDate",
    ]
    return _emit(payload, args, columns=columns)


def cmd_nextup(args: argparse.Namespace, cfg: ServiceConfig) -> int:
    """Jellyfin ``nextup`` — next-up episodes (REQ-6 AC4).

    Accepts optional ``Limit`` and ``StartIndex`` query parameters.
    ``UserId`` is read from ``cfg.jellyfin.user_id`` via
    :func:`_require_user_id` and is required on Jellyfin v12+; the
    universal ``--limit`` flag is forwarded to ``Limit`` and
    ``--start-index`` is a command-local flag registered on the
    subparser for pagination. Mirrors the three sibling
    user-scoped handlers (``resume`` / ``recent`` / ``latest`` /
    ``favorites``).
    """
    user_id = _require_user_id(cfg)
    params: dict[str, Any] = {}
    limit = getattr(args, "limit", None)
    if limit is not None:
        # The CLI stores ``args.limit`` as the page-size requested for
        # ``--human`` view; we reuse the same value for the Jellyfin
        # ``Limit`` query parameter so the server-side cap matches the
        # client-side pagination cap.
        try:
            params["Limit"] = int(limit)
        except (TypeError, ValueError):
            # argparse types ``--limit`` as int already; this branch
            # is defensive against a future change that drops the
            # type annotation.
            pass
    start_index = getattr(args, "start_index", None)
    if start_index is not None:
        try:
            params["StartIndex"] = int(start_index)
        except (TypeError, ValueError):
            pass
    params["UserId"] = user_id

    payload = _get("/Shows/NextUp", args, cfg, params=params, op="nextup")
    columns = [
        "Name",
        "SeriesName",
        "ParentIndexNumber",
        "IndexNumber",
        "PremiereDate",
    ]
    return _emit(payload, args, columns=columns)


def cmd_latest(args: argparse.Namespace, cfg: ServiceConfig) -> int:
    """Jellyfin ``latest`` — latest additions to the user's library (REQ-6 AC5)."""
    user_id = _require_user_id(cfg)
    payload = _get(
        f"/Users/{transport.encode_path_segment(user_id)}/Items/Latest",
        args,
        cfg,
        op="latest",
    )
    # Tabular columns match the summary-shape keys emitted by
    # ``_summary_jellyfin_latest``: the slim DTO returned by
    # ``/Users/{user_id}/Items/Latest`` does not populate
    # ``DateCreated`` on the operator's instance, so the curated
    # summary intentionally drops it. ``DateCreated`` remains
    # available via ``jellyfin item <id>`` (chainable from
    # ``--verbose``).
    columns = ["Name", "Type", "ProductionYear", "SeriesName"]
    return _emit(payload, args, columns=columns)


def cmd_search(args: argparse.Namespace, cfg: ServiceConfig) -> int:
    """Jellyfin ``search <query>`` — search across the library (REQ-6 AC6).

    An empty query is forwarded verbatim; the service returns an
    empty array result rather than a 4xx error, which matches the
    requirement's "otherwise the service's empty-array response"
    branch.

    The ``Recursive=true`` query parameter is REQUIRED on Jellyfin
    v10+/v12: ``GET /Items`` defaults to a non-recursive scan of
    the configured library-root view, so without ``Recursive=true``
    the server returns the library folders themselves (Anime,
    collections, Movies, Playlists, Shows) for every query —
    including no-match and empty queries — rather than walking the
    full library graph and returning the actual matches (or
    ``[]`` for misses). Do not strip this flag.
    """
    query = getattr(args, "query", "") or ""
    payload = _get(
        "/Items",
        args,
        cfg,
        params={"searchTerm": query, "Recursive": True},
        op="search",
    )
    columns = ["Name", "Type", "ProductionYear", "SeriesName"]
    return _emit(payload, args, columns=columns)


def cmd_item(args: argparse.Namespace, cfg: ServiceConfig) -> int:
    """Jellyfin ``item <id>`` — fetch a single item by id (REQ-6 AC7).

    ``UserId`` is read from ``cfg.jellyfin.user_id`` via
    :func:`_require_user_id` and is required on Jellyfin v12+;
    without it the server returns HTTP 400 ``Error processing
    request.`` Mirrors :func:`cmd_nextup`. The transport layer
    maps a 404 response to :class:`HttpError(exit_code=4)` so
    the caller doesn't need to inspect the status code;
    :func:`main_wrapper` then emits the structured stderr line
    naming the id.
    """
    user_id = _require_user_id(cfg)
    raw_id = getattr(args, "item_id", "")
    item_id = transport.encode_path_segment(raw_id)
    payload = _get(
        f"/Items/{item_id}",
        args,
        cfg,
        params={"UserId": user_id},
        op=f"item id={raw_id}",
    )
    columns = ["Name", "Type", "ProductionYear", "Overview", "UserData"]
    return _emit(payload, args, columns=columns)


def cmd_favorites(args: argparse.Namespace, cfg: ServiceConfig) -> int:
    """Jellyfin ``favorites`` -- items the user has marked as favorite (REQ-6 AC8).

    The v12 release removed the dedicated
    ``/Users/<user_id>/Items/Favorites`` sub-resource path; the
    v12-compatible replacement is
    ``/Users/<user_id>/Items?Filters=IsFavorite``, the same shape
    ``cmd_recent`` uses with ``Filters=IsPlayed``.
    """
    user_id = _require_user_id(cfg)
    params: dict[str, Any] = {"Filters": "IsFavorite"}
    limit = getattr(args, "limit", None)
    if limit is not None:
        try:
            params["Limit"] = int(limit)
        except (TypeError, ValueError):
            pass
    payload = _get(
        f"/Users/{transport.encode_path_segment(user_id)}/Items",
        args,
        cfg,
        params=params,
        op="favorites",
    )
    columns = ["Id", "Name", "Type", "ProductionYear", "SeriesName"]
    return _emit(payload, args, columns=columns)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


#: Mapping of subcommand name → handler. Used by the post-parser
#: dispatch step in :func:`main` so every command has a single,
#: auditable registration point. The names are the exact strings the
#: ``argparse`` subparser registers.
_DISPATCH = {
    "now": cmd_now,
    "resume": cmd_resume,
    "recent": cmd_recent,
    "nextup": cmd_nextup,
    "latest": cmd_latest,
    "search": cmd_search,
    "item": cmd_item,
    "favorites": cmd_favorites,
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


def build_jellyfin_parser() -> argparse.ArgumentParser:
    """Build the full argparse parser for the ``jellyfin`` CLI.

    The returned parser already includes the universal flag set
    (registered by :func:`arr_cli.facade.cli_common.build_parser`) and
    the eight Jellyfin subcommands. Exposed for tests so they can
    parse arguments without going through the console-script entry
    point.
    """
    parser = build_parser(
        prog=SERVICE_NAME,
        description=(
            "Read-only CLI for Jellyfin. Eight commands expose "
            "now-playing, resume candidates, recently played, "
            "next-up episodes, latest additions, search, item-by-id, "
            "and favorites."
        ),
    )
    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
        metavar="COMMAND",
        help="Jellyfin subcommand (see below)",
    )

    subparsers.add_parser(
        "now",
        help="list active sessions (GET /Sessions)",
        parents=universal_parents(),
        add_help=False,
    )
    subparsers.add_parser(
        "resume",
        help="list resumable items for the configured user",
        parents=universal_parents(),
        add_help=False,
    )
    subparsers.add_parser(
        "recent",
        help="list recently played items for the configured user",
        parents=universal_parents(),
        add_help=False,
    )

    nextup = subparsers.add_parser(
        "nextup",
        help="list next-up episodes (GET /Shows/NextUp)",
        parents=universal_parents(),
        add_help=False,
    )
    nextup.add_argument(
        "--start-index",
        type=int,
        default=None,
        metavar="N",
        help="pagination offset forwarded to the service as StartIndex",
    )

    subparsers.add_parser(
        "latest",
        help="list latest additions to the configured user's library",
        parents=universal_parents(),
        add_help=False,
    )

    search = subparsers.add_parser(
        "search",
        help="search the library by term (GET /Items?searchTerm=...)",
        parents=universal_parents(),
        add_help=False,
    )
    search.add_argument(
        "query",
        nargs=argparse.OPTIONAL,
        default="",
        help="search term (empty string returns the service's empty array)",
    )

    item = subparsers.add_parser(
        "item",
        help="fetch a single item by id (GET /Items/{id})",
        parents=universal_parents(),
        add_help=False,
    )
    item.add_argument(
        "item_id",
        metavar="ID",
        help="Jellyfin item id (percent-encoded before being sent)",
    )

    subparsers.add_parser(
        "favorites",
        help="list the configured user's favorite items",
        parents=universal_parents(),
        add_help=False,
    )

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """Console-script entry point for the ``jellyfin`` executable.

    Wires the per-service subparser tree to :func:`main_wrapper` so
    config loading, debug/quiet flag handling, and ArrError → stderr
    + exit-code mapping all happen in one place.
    """
    parser = build_jellyfin_parser()
    return main_wrapper(
        SERVICE_NAME,
        _dispatch,
        parser=parser,
        argv=argv,
    )
