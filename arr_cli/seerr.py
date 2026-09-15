"""Seerr CLI (``arr_cli.seerr``) -- task 12.

This module is the Seer entry point for the ``arr-cli`` MVP.
Seer is the unified fork of Overseerr and Jellyseerr; the CLI
talks to whatever Seer instance the operator points it at via
``arr.conf``. It exposes six read-only commands against a live
Seer instance:

* ``requests``            -- ``GET /api/v1/request``                 (REQ-10 AC1)
* ``request-count``       -- ``GET /api/v1/request/count``           (REQ-10 AC2)
* ``search <query>``      -- ``GET /api/v1/search?query=...``       (REQ-10 AC3)
* ``available <query>``   -- ``GET /api/v1/media/available?query=...``(REQ-10 AC4)
* ``media <tmdbId>``      -- ``GET /api/v1/media/{tmdbId}``          (REQ-10 AC5)
* ``user``                -- auth self-check (REQ-10 AC6) with a
                             two-step probe that falls back from
                             ``/api/v1/user/me`` to ``/auth/me`` on
                             a 404 response (design.md "Pre-locking
                             Verifications -- Seerr").

Per the MVP design, every command is a thin wrapper that:

1. Builds the Seerr path (with percent-encoded path segments)
2. Calls :func:`arr_cli.facade.transport.get` to fetch the JSON
3. Renders the payload via :func:`arr_cli.facade.output.emit` (JSON
   pass-through by default, tabular ``--human`` rendering when
   requested)
4. Returns ``0`` on success or reraises the documented
   :class:`ArrError` subclass so :func:`main_wrapper` can translate
   the error into the structured ``service=...`` stderr line and the
   documented exit code.

The auth header is ``X-Api-Key`` (injected transparently by
:func:`arr_cli.facade.transport._inject_auth` per REQ-2 AC3); this
module never references the credential itself.

Following the design contract this module contains no hardcoded URL
or credential; it reads configuration via :class:`ServiceConfig` and
delegates transport / auth / output to the facade. The console-script
entry point declared in ``pyproject.toml`` (``seerr = \
"arr_cli.seerr:main"``) calls :func:`main` which builds the argparse
subparser tree and delegates to the universal
:func:`arr_cli.facade.cli_common.main_wrapper` for config loading and
error mapping.

Seerr-specific behaviour (REQ-10 AC6, AC7; design.md "Pre-locking
Verifications -- Seerr"):

* The ``user`` command implements a two-step auth probe:

    1. Try ``GET /api/v1/user/me`` first (REQ-10 AC6).
    2. If the response is 404, transparently retry ``GET /auth/me``
       (the Overseerr-spec canonical path -- design.md "Pre-locking
       Verifications -- Seerr /api/v1/user/me").
    3. Return whichever succeeds; if both 404, raise
       :class:`HttpError(exit_code=4)` naming the operator's instance.
    4. Both attempts are logged at DEBUG level so the operator can
       see which path was used when investigating an issue.

  The probe is intentionally internal to ``cmd_user``; the ``user``
  subcommand is the only public surface. There is no separate
  ``auth-me`` subcommand.

* ``create-request`` (``POST /api/v1/request``) is **NOT** in MVP and
  MUST NOT appear in ``--help`` (REQ-10 AC7). The dispatch table
  reserves the slot for tier-2 work, where it will be guarded by
  ``--confirm`` per the requirements.
"""

from __future__ import annotations

import argparse
import logging
from typing import Any, Sequence

from arr_cli.facade import output, transport
from arr_cli.facade.cli_common import build_parser, main_wrapper, universal_parents
from arr_cli.facade.config import ServiceConfig
from arr_cli.facade.errors import ConfigError, HttpError

__all__ = [
    "main",
    "build_seerr_parser",
    # Command handlers are exposed for tests; they are not part of the
    # public CLI surface but unit tests use them to verify routing in
    # isolation from the argparse layer.
    "cmd_requests",
    "cmd_request_count",
    "cmd_search",
    "cmd_available",
    "cmd_media",
    "cmd_user",
    # Path constants are exposed so tests can assert against the
    # exact strings and so a future tier-2 command (e.g. ``create-
    # request``) can reuse the prefix without copy-paste drift.
    "USER_ME_PATH",
    "AUTH_ME_FALLBACK_PATH",
]


#: Service identifier used by transport.get / output.emit.
#: Centralised so a future rename touches one constant.
SERVICE_NAME = "seerr"


#: Primary path for the auth self-check per REQ-10 AC6.
#: ``GET /api/v1/user/me`` -- the requirement-specified path.
USER_ME_PATH = "/api/v1/user/me"


#: Fallback path for the auth self-check per design.md "Pre-locking
#: Verifications -- Seerr /api/v1/user/me". The verified Overseerr
#: API spec names this as the canonical path (``GET /auth/me``,
#: no ``/api/v1`` prefix, not under ``/user/``); we fall back to it
#: when ``/api/v1/user/me`` returns 404 so the operator's instance
#: works regardless of which path is actually exposed.
AUTH_ME_FALLBACK_PATH = "/auth/me"


# Module-level logger so the documented DEBUG probe records
# (design.md "Pre-locking Verifications -- Seerr") surface through
# the standard ``logging`` configuration without a private handler.
_logger = logging.getLogger("arr_cli.seerr")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
    "CLI overrides config" policy applies uniformly (REQ-5 AC3). The
    ``op`` parameter is currently informational -- the path argument
    already serves as the documented ``op=`` value surfaced via
    :class:`ArrError` -- but is accepted for symmetry with the other
    CLIs so a future logging hook (e.g. per-call metrics) does not
    require a signature change.
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


def _try_user_path(
    path: str,
    *,
    args: argparse.Namespace,
    cfg: ServiceConfig,
) -> tuple[bool, Any]:
    """Attempt one leg of the ``cmd_user`` probe.

    Returns
    -------
    (succeeded, payload_or_exc)
        ``(True, payload)`` when the request returned a 2xx
        response; ``(False, exc)`` when the request raised an
        :class:`HttpError` with ``status == 404`` (the documented
        trigger for trying the fallback path per design.md
        "Pre-locking Verifications -- Seerr"). Any other error is
        re-raised immediately because it indicates a real failure
        rather than a path-divergence -- the operator deserves to
        see the structured stderr line, not a silent fallback.

    Notes
    -----
    Both attempts are logged at DEBUG level per design.md
    "Pre-locking Verifications -- Seerr": ``seerr: user probe
    <path> -> <status>``. This lets operators see which path was
    used when investigating an issue (e.g. via
    ``--debug``). The probe path itself is also logged at DEBUG
    level so the wrapper layer can audit which endpoint answered.
    """
    debug = bool(getattr(args, "debug", False))
    try:
        payload = _get(
            path,
            args,
            cfg,
            op=f"user probe {path}",
        )
    except HttpError as exc:
        if debug:
            _logger.debug(
                "seerr: user probe %s -> %s", path, exc.status
            )
        if exc.status == 404:
            # Documented fallback trigger (design.md "Pre-locking
            # Verifications -- Seerr"). Return the exception so the
            # caller can decide whether to try the other path or
            # surface the final 404.
            return False, exc
        # Any other HTTP failure (auth, server error, ...) is a real
        # problem and must bubble up unchanged. Re-raising here
        # preserves the structured stderr line and exit code so the
        # operator sees the real failure rather than a misleading
        # "both paths returned 404" message.
        raise

    if debug:
        _logger.debug(
            "seerr: user probe %s -> 200", path
        )
    return True, payload


def cmd_user(args: argparse.Namespace, cfg: ServiceConfig) -> int:
    """Seerr ``user`` -- auth self-check with /api/v1/user/me -> /auth/me fallback (REQ-10 AC6).

    Seer (the unified Overseerr + Jellyseerr fork) diverges on the
    canonical auth-self-check endpoint:

    * The requirements document :data:`USER_ME_PATH`
      (``/api/v1/user/me``).
    * The verified Overseerr API spec documents
      :data:`AUTH_ME_FALLBACK_PATH` (``/auth/me``, no ``/api/v1``
      prefix, not under ``/user/``).

    The design (design.md "Pre-locking Verifications -- Seerr")
    resolves the discrepancy at runtime by trying the requirements
    path first and falling back to the spec path on a 404. Both
    attempts are logged at DEBUG level so the operator can see
    which path was used. If both attempts return 404, the final
    :class:`HttpError(exit_code=4)` surfaces naming both paths so
    the operator can investigate (e.g. via ``--debug``).

    Authentication is handled transparently by the transport layer
    (``X-Api-Key`` header per REQ-2 AC3); this handler does not
    inspect or echo the credential.
    """
    primary_ok, primary_result = _try_user_path(
        USER_ME_PATH, args=args, cfg=cfg
    )
    if primary_ok:
        return _emit(primary_result, args, columns=None)

    # Primary path returned 404; try the documented fallback.
    fallback_ok, fallback_result = _try_user_path(
        AUTH_ME_FALLBACK_PATH, args=args, cfg=cfg
    )
    if fallback_ok:
        return _emit(fallback_result, args, columns=None)

    # Both attempts returned 404. Surface a structured
    # :class:`HttpError(exit_code=4)` naming both paths so the
    # operator can investigate. We rebuild the error here rather
    # than re-raising ``fallback_result`` so the message references
    # the full probe and not just the last leg.
    raise HttpError(
        SERVICE_NAME,
        "user",
        (
            f"{SERVICE_NAME}: user -- HTTP 404 on both "
            f"{USER_ME_PATH} and {AUTH_ME_FALLBACK_PATH}; "
            "verify the auth self-check endpoint on the operator's "
            "instance"
        ),
        status=404,
    )


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


def cmd_requests(args: argparse.Namespace, cfg: ServiceConfig) -> int:
    """Seerr ``requests`` -- the household request queue (REQ-10 AC1).

    Returns the full list of media requests; ``--human`` renders the
    most common columns (``title``, ``type``, ``status``, ``createdAt``).

    The ``take`` query parameter caps the response at the documented
    Seer limit (``1000``) so a single round trip covers the household
    queue instead of the default first page of ten.
    """
    payload = _get(
        "/api/v1/request",
        args,
        cfg,
        params={"take": 1000},
        op="requests",
    )
    # Tabular columns match the summary-shape keys emitted by
    # ``_summary_seerr_requests``: nested ``requestedBy.displayName``
    # is resolved via dot-path traversal in ``_row_from_mapping``.
    columns = [
        "title",
        "type",
        "status",
        "createdAt",
        "requestedBy.displayName",
    ]
    return _emit(payload, args, columns=columns)


def cmd_request_count(args: argparse.Namespace, cfg: ServiceConfig) -> int:
    """Seerr ``request-count`` -- summary counts (REQ-10 AC2).

    Returns a small object whose keys are aggregate counts (e.g.
    ``pending``, ``approved``, ``available``). The ``--human``
    renderer prints the object as ``key: value`` pairs because no
    tabular columns apply to a scalar-ish summary.
    """
    payload = _get(
        "/api/v1/request/count",
        args,
        cfg,
        op="request-count",
    )
    return _emit(payload, args, columns=None)


def cmd_search(args: argparse.Namespace, cfg: ServiceConfig) -> int:
    """Seerr ``search <query>`` -- multi-source search (REQ-10 AC3).

    The ``query`` parameter is forwarded as a query string; the
    transport layer percent-encodes the value so special characters
    (slashes, spaces, ``?``, ``&``) cannot break the URL.

    Targets Seer's consolidated ``/api/v1/search`` endpoint. The
    historical Overseerr path ``/api/v1/search/multi`` is NOT
    exposed by Seer; the live ``/api-docs/swagger-ui-init.js``
    OpenAPI spec is the source of truth (AGENTS.md §1 "Seer note").
    """
    query = getattr(args, "query", "") or ""
    payload = _get(
        "/api/v1/search",
        args,
        cfg,
        params={"query": query},
        op="search",
    )
    # Tabular columns match the summary-shape keys emitted by
    # ``_summary_seerr_search``: nested ``mediaInfo.tmdbId`` is
    # resolved via dot-path traversal in ``_row_from_mapping``.
    columns = [
        "title",
        "mediaType",
        "releaseDate",
        "mediaInfo.tmdbId",
    ]
    return _emit(payload, args, columns=columns)


def cmd_available(args: argparse.Namespace, cfg: ServiceConfig) -> int:
    """Seerr ``available <query>`` -- what's already in the library (REQ-10 AC4).

    Returns the subset of media Seerr considers "available" (i.e.
    already requested and present in the user's library). The
    ``query`` parameter is forwarded as a query string; the
    transport layer percent-encodes the value.
    """
    query = getattr(args, "query", "") or ""
    payload = _get(
        "/api/v1/media/available",
        args,
        cfg,
        params={"query": query},
        op="available",
    )
    # Tabular columns match the summary-shape keys emitted by
    # ``_summary_seerr_available``: nested ``mediaInfo.status`` is
    # resolved via dot-path traversal in ``_row_from_mapping``.
    columns = [
        "title",
        "mediaType",
        "releaseDate",
        "mediaInfo.status",
    ]
    return _emit(payload, args, columns=columns)


def cmd_media(args: argparse.Namespace, cfg: ServiceConfig) -> int:
    """Seerr ``media <tmdbId>`` -- fetch a single media item (REQ-10 AC5).

    The transport layer maps a 404 response to
    :class:`HttpError(exit_code=4)` so the caller doesn't need to
    inspect the status code; :func:`main_wrapper` then emits the
    structured stderr line naming the id.
    """
    raw_id = getattr(args, "tmdb_id", "")
    tmdb_id = transport.encode_path_segment(raw_id)
    payload = _get(
        f"/api/v1/media/{tmdb_id}",
        args,
        cfg,
        op=f"media id={raw_id}",
    )
    columns = [
        "title",
        "mediaType",
        "releaseDate",
        "status",
        "mediaInfo.tmdbId",
    ]
    return _emit(payload, args, columns=columns)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


#: Mapping of subcommand name -> handler. Used by the post-parser
#: dispatch step in :func:`main` so every command has a single,
#: auditable registration point. The names are the exact strings the
#: ``argparse`` subparser registers.
#:
#: Note: ``create-request`` is intentionally NOT in this table.
#: REQ-10 AC7 states "IF a future ``seerr create-request``
#: (``POST /api/v1/request``) is added in tier-2 THEN it SHALL be
#: guarded by a ``--confirm`` flag and SHALL NOT appear in MVP help
#: output". The MVP therefore omits the subcommand and the subparser
#: tree has no entry for it; the test suite asserts the absence.
_DISPATCH = {
    "requests": cmd_requests,
    "request-count": cmd_request_count,
    "search": cmd_search,
    "available": cmd_available,
    "media": cmd_media,
    "user": cmd_user,
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


def build_seerr_parser() -> argparse.ArgumentParser:
    """Build the full argparse parser for the ``seerr`` CLI.

    The returned parser already includes the universal flag set
    (registered by :func:`arr_cli.facade.cli_common.build_parser`) and
    the six Seerr subcommands. Exposed for tests so they can parse
    arguments without going through the console-script entry point.
    """
    parser = build_parser(
        prog=SERVICE_NAME,
        description=(
            "Read-only CLI for Seer (the unified Overseerr + "
            "Jellyseerr fork). Six commands expose the household "
            "request queue, request summary counts, multi-source "
            "search, what's already available in the library, a "
            "single media item by TMDB id, and the current "
            "authenticated user."
        ),
    )
    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
        metavar="COMMAND",
        help="Seerr subcommand (see below)",
    )

    subparsers.add_parser(
        "requests",
        help="list media requests (GET /api/v1/request)",
        parents=universal_parents(),
        add_help=False,
    )

    subparsers.add_parser(
        "request-count",
        help="summary counts (GET /api/v1/request/count)",
        parents=universal_parents(),
        add_help=False,
    )

    search = subparsers.add_parser(
        "search",
        help=(
            "multi-source search by query "
            "(GET /api/v1/search?query=...)"
        ),
        parents=universal_parents(),
        add_help=False,
    )
    search.add_argument(
        "query",
        nargs=argparse.OPTIONAL,
        default="",
        metavar="QUERY",
        help="search query (percent-encoded before being sent)",
    )

    available = subparsers.add_parser(
        "available",
        help=(
            "list media already available in the library, "
            "filtered by query (GET /api/v1/media/available?query=...)"
        ),
        parents=universal_parents(),
        add_help=False,
    )
    available.add_argument(
        "query",
        nargs=argparse.OPTIONAL,
        default="",
        metavar="QUERY",
        help="search query (percent-encoded before being sent)",
    )

    media = subparsers.add_parser(
        "media",
        help="fetch a single media item by TMDB id (GET /api/v1/media/{tmdbId})",
        parents=universal_parents(),
        add_help=False,
    )
    media.add_argument(
        "tmdb_id",
        metavar="TMDBID",
        help="TMDB id (percent-encoded before being sent)",
    )

    subparsers.add_parser(
        "user",
        help=(
            "fetch the current authenticated user (auth self-check; "
            "GET /api/v1/user/me with fallback to /auth/me on 404)"
        ),
        parents=universal_parents(),
        add_help=False,
    )

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """Console-script entry point for the ``seerr`` executable.

    Wires the per-service subparser tree to :func:`main_wrapper` so
    config loading, debug/quiet flag handling, and ArrError -> stderr
    + exit-code mapping all happen in one place.
    """
    parser = build_seerr_parser()
    return main_wrapper(
        SERVICE_NAME,
        _dispatch,
        parser=parser,
        argv=argv,
    )